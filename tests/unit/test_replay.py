"""`postern replay` rebuilds a run from the audit log alone, matching the live tables."""

import hashlib
from typing import Any

from postern.core.audit import GENESIS_HASH, fetch_rows, verify_rows
from postern.core.replay import render_json, render_text, replay_run


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_replay_reproduces_the_run_from_audit_log_alone(db: Any, seed: Any) -> None:
    agent = seed.agent("replay_probe", human_gate=True)
    run_id = seed.run(agent, key="drill:replay")
    seed.transition(run_id, "running")
    for index, (kind, tool) in enumerate([("llm_call", None), ("tool_call", "findings.read")]):
        db.execute(
            "select postern.record_step(%s, %s, %s, %s, 'completed', %s, %s, null, 7)",
            (run_id, index, kind, tool, _hex(f"in{index}"), _hex(f"out{index}")),
        )
    seed.transition(run_id, "awaiting_human")
    db.execute("select postern.record_human_decision(%s, 'approved', null)", (run_id,))
    seed.transition(run_id, "completed")

    replayed = replay_run(db, run_id)

    run = db.execute(
        "select agent, status::text, idempotency_key, manifest_hash, human_decision, trigger"
        " from postern.runs where id = %s",
        (run_id,),
    ).fetchone()
    assert (
        replayed.agent,
        replayed.status,
        replayed.idempotency_key,
        replayed.manifest_hash,
        replayed.human_decision,
        replayed.trigger,
    ) == tuple(run)
    steps = db.execute(
        "select step_index, kind, tool_name, status, input_hash, output_hash from postern.steps"
        " where run_id = %s order by step_index",
        (run_id,),
    ).fetchall()
    assert [
        (s.step_index, s.kind, s.tool_name, s.status, s.input_hash, s.output_hash)
        for s in replayed.steps
    ] == [tuple(step) for step in steps]
    assert [t.to_status for t in replayed.transitions] == [
        "queued",
        "running",
        "awaiting_human",
        "completed",
    ]
    assert replayed.integrity_problems == []

    text = render_text(replayed)
    assert "awaiting_human -> completed" in text
    assert "Audit chain intact" in text
    assert '"intact": true' in render_json(replayed)


def test_python_and_database_agree_on_every_hash(db: Any, seed: Any) -> None:
    run_id = seed.run(seed.agent("hash_probe"))
    seed.transition(run_id, "running")
    database_genesis = db.execute("select postern.audit_genesis_hash()").fetchone()[0]
    assert bytes(database_genesis) == GENESIS_HASH
    rows = fetch_rows(db, run_id=run_id)
    assert len(rows) == 2
    assert verify_rows(rows) == []
