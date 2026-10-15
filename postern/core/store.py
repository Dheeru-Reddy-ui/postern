"""Typed access to the control plane's write functions (ADR-011).

Every method runs in the caller's transaction; the caller commits. All writes go through the
SECURITY DEFINER functions in supabase/schemas/, so the audit log, state machine and warehouse
outbox are maintained by the database, not by this module.
"""

from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class ClaimedRun:
    run_id: str
    msg_id: int
    read_count: int
    resumed: bool
    queue: str


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    agent: str
    tier: int
    manifest_hash: str
    connection_id: str | None
    user_id: str | None
    status: str


@dataclass(frozen=True)
class AgentRecord:
    name: str
    status: str
    tier: int
    manifest: dict[str, Any]
    manifest_hash: str


@dataclass(frozen=True)
class ConnectionRecord:
    connection_id: str
    user_id: str
    project_ref: str
    status: str
    publishable_key: str | None
    verified_at: str | None


@dataclass(frozen=True)
class StepRecord:
    index: int
    kind: str
    tool_name: str | None
    status: str
    output: Any


class Store:
    def __init__(self, conn: Connection[Any]) -> None:
        self.conn = conn

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # ------------------------------------------------------------------- queue

    def claim_next_run(
        self, queue: str, visibility_seconds: int, max_attempts: int
    ) -> ClaimedRun | None:
        row = self.conn.execute(
            "select claimed_run_id::text, claimed_msg_id, read_count, resumed"
            " from postern.claim_next_run(%s, %s, %s)",
            (queue, visibility_seconds, max_attempts),
        ).fetchone()
        if row is None:
            return None
        return ClaimedRun(str(row[0]), int(row[1]), int(row[2]), bool(row[3]), queue)

    def extend_claim(self, claimed: ClaimedRun, visibility_seconds: int) -> None:
        self.conn.execute(
            "select postern.extend_claim(%s, %s, %s)",
            (claimed.queue, claimed.msg_id, visibility_seconds),
        )

    def finish_run(self, claimed: ClaimedRun, status: str, error_code: str | None) -> None:
        self.conn.execute(
            "select postern.finish_run(%s, %s, %s, %s::postern.run_status, %s)",
            (claimed.run_id, claimed.queue, claimed.msg_id, status, error_code),
        )

    # ------------------------------------------------------------------- reads

    def load_run(self, run_id: str) -> RunRecord:
        row = self.conn.execute(
            "select id::text, agent, tier, manifest_hash, connection_id::text, user_id::text,"
            " status::text from postern.runs where id = %s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"run {run_id} does not exist")
        return RunRecord(
            str(row[0]), str(row[1]), int(row[2]), str(row[3]), row[4], row[5], str(row[6])
        )

    def load_agent(self, name: str) -> AgentRecord:
        row = self.conn.execute(
            "select name, status::text, tier, manifest, manifest_hash from postern.agents"
            " where name = %s",
            (name,),
        ).fetchone()
        if row is None:
            raise LookupError(f"agent {name} is not registered")
        return AgentRecord(str(row[0]), str(row[1]), int(row[2]), dict(row[3]), str(row[4]))

    def load_connection(self, connection_id: str) -> ConnectionRecord:
        row = self.conn.execute(
            "select id::text, user_id::text, supabase_project_ref, status, publishable_key,"
            " verified_at::text from postern.connections where id = %s",
            (connection_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"connection {connection_id} does not exist")
        return ConnectionRecord(str(row[0]), str(row[1]), str(row[2]), str(row[3]), row[4], row[5])

    def completed_steps(self, run_id: str) -> list[StepRecord]:
        rows = self.conn.execute(
            "select step_index, kind, tool_name, status, output from postern.steps"
            " where run_id = %s order by step_index",
            (run_id,),
        ).fetchall()
        return [StepRecord(int(r[0]), str(r[1]), r[2], str(r[3]), r[4]) for r in rows]

    def llm_spend_this_month(self) -> float:
        row = self.conn.execute("select postern.llm_spend_this_month()").fetchone()
        return float(row[0]) if row else 0.0

    def stale_connections(self, hours: int) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "select connection_id::text, project_ref from postern.stale_connections(%s)",
            (hours,),
        ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    # ------------------------------------------------------------------ writes

    def record_step(
        self,
        run_id: str,
        index: int,
        kind: str,
        tool_name: str | None,
        status: str,
        input_hash: str,
        output_hash: str | None,
        output: Any,
        refusal_reason: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.conn.execute(
            "select postern.record_step(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id,
                index,
                kind,
                tool_name,
                status,
                input_hash,
                output_hash,
                refusal_reason,
                duration_ms,
                Jsonb(output),
            ),
        )

    def write_audit(
        self,
        event_type: str,
        run_id: str | None,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None:
        self.conn.execute(
            "select postern.write_audit(%s, %s, %s, %s, %s)",
            (event_type, run_id, entity_type, entity_id, Jsonb(payload)),
        )

    def record_finding(self, run_id: str, finding: dict[str, Any]) -> str:
        row = self.conn.execute(
            "select postern.record_finding(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id,
                finding["fingerprint"],
                finding["finding_type"],
                finding["category"],
                finding["severity"],
                finding["source"],
                finding.get("object_schema"),
                finding.get("object_name"),
                finding.get("title"),
                finding.get("probe_http_status"),
                finding.get("probe_row_count"),
                bool(finding.get("probe_confirmed", False)),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("record_finding returned nothing")
        return str(row[0])

    def set_finding_explanation(
        self, run_id: str, finding_id: str, explanation: str, model: str
    ) -> None:
        self.conn.execute(
            "select postern.set_finding_explanation(%s, %s, %s, %s)",
            (run_id, finding_id, explanation, model),
        )

    def mark_connection_checked(self, run_id: str) -> None:
        self.conn.execute("select postern.mark_connection_checked(%s)", (run_id,))

    def set_connection_status(self, connection_id: str, status: str, reason: str) -> None:
        self.conn.execute(
            "select postern.set_connection_status(%s, %s, %s)", (connection_id, status, reason)
        )
