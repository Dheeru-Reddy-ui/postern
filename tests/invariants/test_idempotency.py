"""Threat-model tests: TM-18. See docs/threat-model.md."""

from typing import Any

import psycopg
import pytest


def test_duplicate_idempotency_key_cannot_create_second_run(db: Any, seed: Any) -> None:
    """runs.idempotency_key is unique."""
    agent = seed.agent("idem_probe")
    first = seed.run(agent, key="nightly:idem_probe:2026-10-14")
    second = seed.run(agent, key="nightly:idem_probe:2026-10-14")
    assert first == second

    def count(sql: str) -> int:
        row = db.execute(sql, (first,)).fetchone()
        return int(row[0])

    enqueued = "select count(*) from postern.audit_log where run_id = %s and event_type = %s"
    assert count("select count(*) from postern.runs where id = %s") == 1
    assert count("select count(*) from pgmq.q_tier0 where message ->> 'run_id' = %s::text") == 1
    assert db.execute(enqueued, (first, "run.enqueued")).fetchone()[0] == 1

    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "insert into postern.runs (agent, tier, manifest_hash, trigger, idempotency_key)"
            " values (%s, 0, %s, 'drill', 'nightly:idem_probe:2026-10-14')",
            (agent, "0" * 64),
        )


def test_one_open_proposal_per_finding_fingerprint(db: Any, seed: Any) -> None:
    """There is at most one open proposal per finding fingerprint (unique partial index)."""
    user = seed.user()
    connection = seed.connection(user)
    agent = seed.agent("proposal_probe")
    finding = seed.finding(user, connection, agent)
    fingerprint = db.execute(
        "select fingerprint from postern.findings where id = %s", (finding,)
    ).fetchone()[0]

    def propose(status: str) -> None:
        db.execute(
            "insert into postern.proposals (finding_id, fingerprint, connection_id, user_id,"
            " status, branch) values (%s, %s, %s, %s, %s, 'postern/fix-rls')",
            (finding, fingerprint, connection, user, status),
        )

    propose("declined")
    propose("merged")
    propose("pr_open")
    with pytest.raises(psycopg.errors.UniqueViolation):
        propose("drafting")


def test_duplicate_alert_is_suppressed(db: Any, seed: Any) -> None:
    """Notifications carry a unique dedupe key."""
    user = seed.user()

    def notify() -> None:
        db.execute(
            "insert into postern.notifications (user_id, channel, kind, dedupe_key, status,"
            " template, template_version)"
            " values (%s, 'email', 'realtime_alert', 'alert:exposure:abc', 'sent', 'alert', '1')",
            (user,),
        )

    notify()
    with pytest.raises(psycopg.errors.UniqueViolation):
        notify()
