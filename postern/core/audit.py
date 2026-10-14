"""Read and independently verify Postern's hash-chained audit log (TM-22).

The canonical row text and hash here mirror postern.audit_row_text / postern.audit_row_hash
in supabase/schemas/20_postern_audit.sql. Verifying in Python as well as in SQL means a
tampered database function can't vouch for itself.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from psycopg import Connection, sql

GENESIS_HASH = hashlib.sha256(b"postern-audit-genesis-v1").digest()
_SEPARATOR = "\x1f"

_COLUMNS = sql.SQL(
    "chain_position,"
    " to_char(occurred_at at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'),"
    " txid, actor, event_type, run_id::text, entity_type, entity_id, payload::text,"
    " prev_hash, row_hash"
)


@dataclass(frozen=True)
class AuditRow:
    chain_position: int
    occurred_at: str
    txid: int
    actor: str
    event_type: str
    run_id: str | None
    entity_type: str | None
    entity_id: str | None
    payload_text: str
    prev_hash: bytes
    row_hash: bytes

    @property
    def payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_text)
        if not isinstance(value, dict):
            raise ValueError(f"audit row {self.chain_position} payload is not an object")
        return value

    def canonical_text(self) -> str:
        return _SEPARATOR.join(
            (
                str(self.chain_position),
                self.occurred_at,
                str(self.txid),
                self.actor,
                self.event_type,
                self.run_id or "",
                self.entity_type or "",
                self.entity_id or "",
                self.payload_text,
            )
        )

    def expected_hash(self) -> bytes:
        return hashlib.sha256(self.prev_hash + self.canonical_text().encode("utf-8")).digest()


@dataclass(frozen=True)
class ChainProblem:
    chain_position: int
    problem: str


def _row(values: Sequence[Any]) -> AuditRow:
    return AuditRow(
        chain_position=int(values[0]),
        occurred_at=str(values[1]),
        txid=int(values[2]),
        actor=str(values[3]),
        event_type=str(values[4]),
        run_id=values[5],
        entity_type=values[6],
        entity_id=values[7],
        payload_text=str(values[8]),
        prev_hash=bytes(values[9]),
        row_hash=bytes(values[10]),
    )


def fetch_rows(conn: Connection[Any], run_id: str | None = None) -> list[AuditRow]:
    """All audit rows in chain order, or only one run's rows."""
    query = sql.SQL("select {} from postern.audit_log").format(_COLUMNS)
    if run_id is None:
        rows = conn.execute(query + sql.SQL(" order by chain_position")).fetchall()
    else:
        rows = conn.execute(
            query + sql.SQL(" where run_id = %s order by chain_position"), (run_id,)
        ).fetchall()
    return [_row(row) for row in rows]


def verify_rows(rows: Sequence[AuditRow]) -> list[ChainProblem]:
    """Check each row's hash against its own contents (works on any subset of the log)."""
    return [
        ChainProblem(row.chain_position, "row_hash does not match the row contents")
        for row in rows
        if row.expected_hash() != row.row_hash
    ]


def verify_chain(rows: Sequence[AuditRow]) -> list[ChainProblem]:
    """Check the whole log: contents, positions with no gaps, and every link."""
    problems = verify_rows(rows)
    previous_hash = GENESIS_HASH
    for expected_position, row in enumerate(rows, start=1):
        if row.chain_position != expected_position:
            problems.append(ChainProblem(row.chain_position, "gap before this row"))
        if row.prev_hash != previous_hash:
            problems.append(
                ChainProblem(row.chain_position, "prev_hash does not match the previous row")
            )
        previous_hash = row.row_hash
    return sorted(problems, key=lambda problem: problem.chain_position)


def verify_in_database(conn: Connection[Any]) -> list[ChainProblem]:
    rows = conn.execute(
        "select chain_position, problem from postern.verify_audit_chain()"
    ).fetchall()
    return [ChainProblem(int(position), str(problem)) for position, problem in rows]
