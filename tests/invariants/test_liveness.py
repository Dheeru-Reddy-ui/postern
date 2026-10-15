"""Threat-model tests: TM-27. See docs/threat-model.md."""

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from support import FakeSupabase, SavepointStore, SentinelWorld, TransactionDatabase

from postern.core.worker import alert_stale_connections
from postern.integrations.supabase_mgmt.broker import ConnectionUnavailable, TokenService
from postern.integrations.supabase_mgmt.tokens import TokenCipher


def test_missed_nightly_run_raises_alert(
    db: psycopg.Connection[Any],
    seed: Any,
    caplog: pytest.LogCaptureFixture,
    terraform: Callable[[str], Any],
) -> None:
    """A dead-man's switch alerts the operator when a connection's nightly run is missing."""
    user = seed.user()
    never_checked = seed.active_connection(user)
    checked_today = seed.active_connection(seed.user())
    brand_new = seed.active_connection(seed.user())
    revoked = seed.active_connection(seed.user())
    db.execute(
        "update postern.connections set created_at = now() - interval '30 hours'"
        " where id = any(%s::uuid[])",
        ([never_checked, checked_today, revoked],),
    )
    db.execute(
        "update postern.connections set last_checked_at = now() - interval '2 hours' where id = %s",
        (checked_today,),
    )
    db.execute("select postern.set_connection_status(%s, 'revoked', 'test')", (revoked,))

    store = SavepointStore(db)
    with caplog.at_level(logging.CRITICAL, logger="postern.worker"):
        alert_stale_connections(store, 26)
    alerted = {
        record.args[0]  # type: ignore[index]
        for record in caplog.records
        if getattr(record, "postern_alert", None) == "missed_nightly_run"
    }
    mine = {never_checked, checked_today, brand_new, revoked}
    assert alerted & mine == {never_checked}

    # The chain that should have checked it: pg_cron enqueues at 02:17 UTC, Cloud Scheduler
    # starts the worker at 02:30 UTC, and any alert the worker logs is emailed.
    job = db.execute(
        "select schedule from cron.job where jobname = 'postern-nightly-runs'"
    ).fetchone()
    assert job == ("17 2 * * *",)
    gcp = terraform("infra/terraform/gcp")
    scheduler = gcp.resources("google_cloud_scheduler_job")["nightly_worker"]
    assert (scheduler["schedule"], scheduler["time_zone"]) == ("30 2 * * *", "Etc/UTC")
    assert scheduler["http_target"][0]["uri"].endswith(":run")
    policy = gcp.resources("google_monitoring_alert_policy")["postern_alert"]
    log_filter = str(policy["conditions"][0]["condition_matched_log"][0]["filter"])
    assert "jsonPayload.postern_alert:*" in log_filter
    assert policy["notification_channels"]


def test_revoked_connection_is_reported_not_skipped(
    db: psycopg.Connection[Any],
    seed: Any,
    sentinel_world: Callable[..., SentinelWorld],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A revoked or expired connection fails its run with the reason recorded and alerts the
    operator; it is never skipped quietly or counted as a clean check."""

    def revoked(connection_id: str) -> str:
        raise ConnectionUnavailable("connection_revoked")

    world = sentinel_world(token_for=revoked)
    with caplog.at_level(logging.CRITICAL, logger="postern.runtime"):
        run_id, outcome = world.run()
    assert (outcome.status, outcome.error_code) == (
        "failed",
        "connection_unavailable:connection_revoked",
    )
    assert world.run_status(run_id) == ("failed", "connection_unavailable:connection_revoked")
    alerts = [r for r in caplog.records if getattr(r, "postern_alert", "").startswith("connection")]
    assert alerts, "the operator was alerted"
    checked = db.execute(
        "select last_checked_at from postern.connections where id = %s", (world.connection_id,)
    ).fetchone()
    assert checked == (None,)

    # The broker revokes a connection whose refresh token stops working, and destroys its
    # tokens, so the next run reports it instead of trying again.
    cipher = TokenCipher(os.urandom(32))
    connection = seed.active_connection(seed.user())
    db.execute(
        "update postern.connections set access_token_ciphertext = %s,"
        " refresh_token_ciphertext = %s, token_expires_at = %s where id = %s",
        (
            cipher.encrypt("old"),
            cipher.encrypt("refresh"),
            datetime.now(UTC) - timedelta(minutes=1),
            connection,
        ),
    )
    refused = FakeSupabase(seed.project_ref(connection), [], token_status=400)
    database: Any = TransactionDatabase(db)
    service = TokenService(database, cipher, refused.client(), "id", "secret")
    with pytest.raises(ConnectionUnavailable, match="refresh_failed"):
        service.access_token_for(connection)
    row = db.execute(
        "select status, status_reason, access_token_ciphertext, refresh_token_ciphertext"
        " from postern.connections where id = %s",
        (connection,),
    ).fetchone()
    assert row == ("revoked", "refresh_failed", None, None)
    with pytest.raises(ConnectionUnavailable, match="connection_revoked"):
        service.access_token_for(connection)


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-27 · prevention lands in Phase 6"
)
def test_revoked_connection_is_explained_to_the_user() -> None:
    """The owner of a revoked or expired connection is told, in plain words, that Postern can't
    see their project anymore and how to reconnect."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-27 · prevention lands in Phase 6"
)
def test_reports_show_last_successful_check_time() -> None:
    """Every report and digest shows the time of the last successful check."""
    raise NotImplementedError
