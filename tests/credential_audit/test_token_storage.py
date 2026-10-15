"""Threat-model tests: TM-05. See docs/threat-model.md."""

import io
import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from cryptography.exceptions import InvalidTag
from support import REPO_ROOT, FakeSupabase, SentinelWorld, create_connect_link, jwt

from postern.agents.toolkit import build_toolkit
from postern.config import load_config
from postern.core.redaction import REDACTED, JsonFormatter, RedactingFilter, find_secrets
from postern.core.runtime import RunContext
from postern.integrations.probe import ExposureProbe
from postern.integrations.supabase_mgmt.tokens import HEADER, TokenCipher
from postern.llm.gateway import GatewayRequest, LLMGateway, PromptRejected

# Fake credentials, one of each shape Postern must never log or send to a model.
SECRET_KEY = "sb_secret_" + "N7xQ2pLk9vRt4mZs8wYb"
ACCESS_TOKEN = "sbp_" + "0123456789abcdef" * 2 + "01234567"
SERVICE_ROLE = jwt({"role": "service_role", "ref": "abcdefghijklmnopqrst"})
DATABASE_URL = "postgresql://postgres:" + "hunter2hunter2" + "@db.example.co:5432/postgres"
ANON_KEY = jwt({"role": "anon", "ref": "abcdefghijklmnopqrst"})


def test_oauth_tokens_encrypted_at_rest(
    db: psycopg.Connection[Any], seed: Any, connect_client: Callable[..., Any]
) -> None:
    """User OAuth tokens are encrypted at rest with a key the database never holds (envelope
    encryption, key in Secret Manager)."""
    key = os.urandom(32)
    cipher = TokenCipher(key)
    envelope = cipher.encrypt("sbp_oauth_access_example")
    assert envelope.startswith(HEADER)
    assert b"sbp_oauth_access_example" not in envelope
    assert cipher.encrypt("same") != cipher.encrypt("same"), "a fresh nonce every time"
    assert cipher.decrypt(envelope) == "sbp_oauth_access_example"
    with pytest.raises(InvalidTag):
        TokenCipher(os.urandom(32)).decrypt(envelope)
    with pytest.raises(InvalidTag):
        cipher.decrypt(envelope[:-1] + bytes([envelope[-1] ^ 1]))

    # The database refuses anything that isn't an envelope, so plaintext can't be stored.
    user = seed.user()
    connection = seed.connection(user)
    with pytest.raises(psycopg.errors.CheckViolation, match="tokens_are_envelope_ciphertext"):
        db.execute(
            "update postern.connections set access_token_ciphertext = %s where id = %s",
            (b"sbp_oauth_access_plaintext", connection),
        )
    db.rollback()

    # Through the real OAuth callback: what lands in the database is ciphertext under the
    # API's key, and neither the token nor the key appears anywhere in the row.
    user = seed.user()
    fake = FakeSupabase("abcdefghijklmnopqrst", [])
    client = connect_client(fake, cipher)
    start = client.get(
        "/connect/supabase", params={"token": create_connect_link(db, user, fake.ref)}
    )
    state = httpx.URL(start.headers["location"]).params["state"]
    done = client.get("/oauth/supabase/callback", params={"code": "c0de", "state": state})
    assert done.status_code == 200, done.text
    row = db.execute(
        "select access_token_ciphertext, refresh_token_ciphertext, row_to_json(c)::text"
        " from postern.connections c where user_id = %s",
        (user,),
    ).fetchone()
    assert row is not None
    access, refresh, whole_row = bytes(row[0]), bytes(row[1]), row[2]
    assert cipher.decrypt(access) == "sbp_oauth_access_c0de"
    assert cipher.decrypt(refresh) == "refresh-c0de"
    assert "sbp_oauth_access_c0de" not in whole_row
    assert key.hex() not in whole_row

    # Only postern-api reads the key from its environment; the worker never loads it.
    loaders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "postern").rglob("*.py")
        if "TokenCipher.from_env" in path.read_text(encoding="utf-8")
    ]
    assert loaders == ["postern/api/connect.py"]


class NoDatabase:
    autocommit = True

    def execute(self, *args: Any) -> Any:
        raise AssertionError("a rejected prompt must not reach the database")


def test_secrets_never_appear_in_logs_or_prompts(
    sentinel_world: Callable[..., SentinelWorld],
) -> None:
    """Logs and prompts pass a redaction filter, and the gateway refuses prompts containing
    secret-shaped strings."""
    secrets = [SECRET_KEY, ACCESS_TOKEN, SERVICE_ROLE, DATABASE_URL, "Bearer " + "x" * 32]
    for value in secrets:
        assert find_secrets(f"context {value} context"), value
    assert find_secrets(ANON_KEY) == [], "the anon key is public by design"

    # Logs: every record goes through the redacting filter and formatter.
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    log = logging.getLogger("postern.test.redaction")
    log.addHandler(handler)
    log.propagate = False
    try:
        for value in secrets:
            log.warning("leaked %s here", value)
            log.warning("leaked " + value)
    finally:
        log.removeHandler(handler)
    output = stream.getvalue()
    assert output.count(REDACTED) == 2 * len(secrets)
    for value in secrets:
        assert value not in output
    assert "hunter2hunter2" not in output
    for line in output.splitlines():
        json.loads(line)  # still one structured entry per line

    # Prompts: the gateway refuses before reserving spend or calling a model.
    calls: list[Any] = []
    gateway = LLMGateway(
        NoDatabase(),  # type: ignore[arg-type]
        load_config().llm,
        "test-project",
        completion=lambda **kwargs: calls.append(kwargs),
        cost=lambda response: 0.0,
    )
    for value in secrets:
        with pytest.raises(PromptRejected):
            gateway.complete(GatewayRequest("run", 0, "agent", "explain", f"Explain {value}"))
    assert calls == []

    # Model output is checked too: an explanation carrying a secret is dropped.
    tools = build_toolkit(
        lambda connection_id: "token",
        httpx.Client(),
        ExposureProbe(httpx.Client(), load_config().probe),
    )
    recorded: list[str] = []

    class RecordingStore:
        def set_finding_explanation(self, run: str, finding: str, text: str, model: str) -> None:
            recorded.append(text)

    store: Any = RecordingStore()

    ctx = RunContext("run", "security_sentinel", "u", None)
    result = tools["findings.explain"](
        ctx,
        {
            "explanations": [
                {"finding_id": "f1", "text": f"Use {SECRET_KEY}"},
                {"finding_id": "f2", "text": "Turn on RLS."},
            ]
        },
        store,
    )
    assert result == {"explained": 1}
    assert recorded == ["Turn on RLS."]

    # And in a real run, the owner's public key and the access token stay out of prompts.
    world = sentinel_world()
    world.run()
    for call in world.model.calls:
        prompt = call["messages"][0]["content"]
        assert find_secrets(prompt) == []
        assert "test-access-token" not in prompt
        assert not re.search(r"sb_publishable_\S+", prompt)


def test_terraform_state_bucket_is_private_and_versioned(terraform: Callable[[str], Any]) -> None:
    """Terraform state holds Postern's own control-plane database password, so the state bucket
    is private, versioned and protected from deletion."""
    buckets = terraform("infra/terraform/gcp/bootstrap").resources("google_storage_bucket")
    assert set(buckets) == {"tfstate"}
    bucket = buckets["tfstate"]
    assert bucket["uniform_bucket_level_access"] is True
    assert bucket["public_access_prevention"] == "enforced"
    assert bucket["force_destroy"] is False
    assert bucket["versioning"][0]["enabled"] is True
    assert bucket["lifecycle"][0]["prevent_destroy"] is True
    # No IAM on the bucket beyond the project owner's: nothing grants access to it.
    for kind in ("google_storage_bucket_iam_member", "google_storage_bucket_iam_binding"):
        for root in ("infra/terraform/gcp/bootstrap", "infra/terraform/gcp"):
            assert not terraform(root).resources(kind), f"{root} grants access via {kind}"
