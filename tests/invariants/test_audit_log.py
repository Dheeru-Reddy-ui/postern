"""Threat-model tests: TM-22. See docs/threat-model.md."""

from typing import Any

import psycopg
import pytest

from postern.core.audit import fetch_rows, verify_chain, verify_in_database

# The table owner (no SET ROLE), both component roles, and every Supabase API role.
ROLES: tuple[str | None, ...] = (
    None,
    "postern_api",
    "postern_worker",
    "authenticated",
    "anon",
    "service_role",
)


def _attempt_as_each_role(db: psycopg.Connection[Any], statement: str, *params: Any) -> None:
    for role in ROLES:
        db.execute("savepoint attempt")
        if role:
            db.execute(f"set local role {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(statement, params)
        db.execute("rollback to savepoint attempt")


def test_audit_log_rows_cannot_be_updated_or_deleted(db: Any, seed: Any) -> None:
    """UPDATE and DELETE are revoked from every Postern role, and a trigger blocks them."""
    run_id = seed.run(seed.agent("audit_probe"))
    _attempt_as_each_role(
        db, "update postern.audit_log set actor = 'tampered' where run_id = %s", run_id
    )
    _attempt_as_each_role(db, "delete from postern.audit_log where run_id = %s", run_id)


def test_audit_log_cannot_be_truncated(db: Any, seed: Any) -> None:
    """TRUNCATE is revoked and blocked by a statement-level trigger. Row triggers don't fire on
    TRUNCATE."""
    seed.run(seed.agent("audit_probe"))
    _attempt_as_each_role(db, "truncate postern.audit_log")


def test_audit_log_hash_chain_detects_tampering(db: Any, seed: Any) -> None:
    """Each row stores the hash of the previous row, so any edit breaks the chain."""
    run_id = seed.run(seed.agent("audit_probe"))
    seed.transition(run_id, "running")
    seed.transition(run_id, "failed")
    assert verify_in_database(db) == []
    assert verify_chain(fetch_rows(db)) == []

    # Only the table owner can even try: it must first switch off the append-only guard.
    db.execute("alter table postern.audit_log disable trigger audit_log_no_update_or_delete")
    edited = db.execute(
        'update postern.audit_log set payload = payload || \'{"to": "completed"}\''
        " where run_id = %s and payload ->> 'to' = 'failed' returning chain_position",
        (run_id,),
    ).fetchone()[0]
    assert edited in {p.chain_position for p in verify_in_database(db)}
    assert edited in {p.chain_position for p in verify_chain(fetch_rows(db))}

    removed = db.execute(
        "delete from postern.audit_log where run_id = %s and payload ->> 'to' = 'running'"
        " returning chain_position",
        (run_id,),
    ).fetchone()[0]
    gaps = {p.chain_position for p in verify_chain(fetch_rows(db)) if "gap" in p.problem}
    assert removed + 1 in gaps


def test_state_change_without_audit_entry_is_rejected(db: Any, seed: Any) -> None:
    """A state change without its audit row in the same transaction is rejected."""
    run_id = seed.run(seed.agent("audit_probe"))
    # Bypass postern.transition_run: the change itself is legal, but nothing audits it.
    db.execute("update postern.runs set status = 'running' where id = %s", (run_id,))
    with pytest.raises(psycopg.errors.CheckViolation, match="without an audit entry"):
        db.execute("set constraints all immediate")
