"""Threat-model tests: TM-24. See docs/threat-model.md."""

from itertools import product
from typing import Any

import psycopg
import pytest

STATUSES = ("queued", "running", "awaiting_human", "completed", "failed", "rolled_back")
ALLOWED = {
    ("queued", "running"),
    ("queued", "failed"),
    ("running", "awaiting_human"),
    ("running", "completed"),
    ("running", "failed"),
    ("awaiting_human", "running"),
    ("awaiting_human", "completed"),
    ("awaiting_human", "failed"),
    ("awaiting_human", "rolled_back"),
}
# How to reach each status from queued, through legal moves only.
PATHS = {
    "queued": [],
    "running": ["running"],
    "awaiting_human": ["running", "awaiting_human"],
    "completed": ["running", "completed"],
    "failed": ["failed"],
    "rolled_back": ["running", "awaiting_human", "rolled_back"],
}


def test_illegal_run_state_transitions_are_rejected(db: Any, seed: Any) -> None:
    """Run state transitions are enforced in the database. Illegal transitions are rejected."""
    agent = seed.agent("state_probe")
    for source, target in product(STATUSES, STATUSES):
        if source == target:
            continue
        db.execute("savepoint attempt")
        run_id = seed.run(agent)
        for step in PATHS[source]:
            seed.transition(run_id, step)
        if (source, target) in ALLOWED:
            seed.transition(run_id, target)
        else:
            with pytest.raises(psycopg.errors.CheckViolation, match="illegal run transition"):
                seed.transition(run_id, target)
        db.execute("rollback to savepoint attempt")

    # Updating the table directly doesn't get around the state machine either.
    run_id = seed.run(agent)
    with pytest.raises(psycopg.errors.CheckViolation, match="illegal run transition"):
        db.execute("update postern.runs set status = 'completed' where id = %s", (run_id,))


def test_human_gated_run_cannot_complete_without_human_decision(db: Any, seed: Any) -> None:
    """A run for a human-gated agent can't reach completed without a recorded human decision."""
    agent = seed.agent("gated_probe", human_gate=True)

    run_id = seed.run(agent)
    seed.transition(run_id, "running")
    db.execute("savepoint straight_to_completed")
    with pytest.raises(psycopg.errors.CheckViolation, match="approved human decision"):
        seed.transition(run_id, "completed")
    db.execute("rollback to savepoint straight_to_completed")

    db.execute("savepoint declined")
    seed.transition(run_id, "awaiting_human")
    db.execute("select postern.record_human_decision(%s, 'declined', null)", (run_id,))
    with pytest.raises(psycopg.errors.CheckViolation, match="approved human decision"):
        seed.transition(run_id, "completed")
    db.execute("rollback to savepoint declined")

    seed.transition(run_id, "awaiting_human")
    db.execute("select postern.record_human_decision(%s, 'approved', null)", (run_id,))
    seed.transition(run_id, "completed")
    status = db.execute("select status from postern.runs where id = %s", (run_id,)).fetchone()[0]
    assert status == "completed"


def test_human_decision_must_come_from_project_owner(db: Any, seed: Any) -> None:
    """A human decision must come from the owner of the connection, whether through the web or
    through postern-mcp."""
    owner, stranger = seed.user(), seed.user()
    agent = seed.agent("owned_probe", human_gate=True)
    run_id = seed.run(agent, connection=seed.connection(owner))
    seed.transition(run_id, "running")
    seed.transition(run_id, "awaiting_human")

    db.execute("savepoint stranger")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only the owner"):
        db.execute("select postern.record_human_decision(%s, 'approved', %s)", (run_id, stranger))
    db.execute("rollback to savepoint stranger")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only the owner"):
        db.execute("select postern.record_human_decision(%s, 'approved', null)", (run_id,))
    db.execute("rollback to savepoint stranger")

    db.execute("select postern.record_human_decision(%s, 'approved', %s)", (run_id, owner))
    seed.transition(run_id, "completed")
