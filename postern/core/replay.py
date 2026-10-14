"""`postern replay <run_id>`: rebuild a run from the audit log alone.

Replay reads nothing but postern.audit_log. It checks every row it uses against its hash and
asks the database to verify the whole chain, then folds the events into the run's history:
how it was enqueued, every state transition, every checkpointed step and any human decision.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from psycopg import Connection

from postern.core.audit import AuditRow, ChainProblem, fetch_rows, verify_in_database, verify_rows


class ReplayError(LookupError):
    """The run has no audit history, or its history is malformed."""


@dataclass(frozen=True)
class Transition:
    chain_position: int
    occurred_at: str
    actor: str
    from_status: str | None
    to_status: str
    error_code: str | None


@dataclass(frozen=True)
class ReplayedStep:
    chain_position: int
    occurred_at: str
    step_index: int
    kind: str
    tool_name: str | None
    status: str
    refusal_reason: str | None
    input_hash: str
    output_hash: str | None
    duration_ms: int | None


@dataclass(frozen=True)
class HumanDecision:
    chain_position: int
    occurred_at: str
    decision: str
    decided_by: str | None


@dataclass
class ReplayedRun:
    run_id: str
    agent: str
    tier: int
    trigger: str
    idempotency_key: str
    manifest_hash: str
    connection_id: str | None
    user_id: str | None
    enqueued_at: str
    status: str
    transitions: list[Transition] = field(default_factory=list)
    steps: list[ReplayedStep] = field(default_factory=list)
    human_decisions: list[HumanDecision] = field(default_factory=list)
    other_events: list[str] = field(default_factory=list)
    integrity_problems: list[ChainProblem] = field(default_factory=list)

    @property
    def human_decision(self) -> str | None:
        return self.human_decisions[-1].decision if self.human_decisions else None

    def to_json(self) -> dict[str, Any]:
        document = asdict(self)
        document["human_decision"] = self.human_decision
        document["intact"] = not self.integrity_problems
        return document


def rebuild(rows: list[AuditRow]) -> ReplayedRun:
    """Fold one run's audit rows, in chain order, into its history."""
    if not rows:
        raise ReplayError("no audit history for this run")
    first = rows[0]
    if first.event_type != "run.enqueued":
        raise ReplayError(f"history starts with {first.event_type}, not run.enqueued")
    start = first.payload
    run = ReplayedRun(
        run_id=str(first.run_id),
        agent=str(start["agent"]),
        tier=int(start["tier"]),
        trigger=str(start["trigger"]),
        idempotency_key=str(start["idempotency_key"]),
        manifest_hash=str(start["manifest_hash"]),
        connection_id=start.get("connection_id"),
        user_id=start.get("user_id"),
        enqueued_at=first.occurred_at,
        status="queued",
    )
    run.transitions.append(
        Transition(first.chain_position, first.occurred_at, first.actor, None, "queued", None)
    )
    for row in rows[1:]:
        payload = row.payload
        if row.event_type == "run.transitioned":
            if payload["from"] != run.status:
                raise ReplayError(
                    f"row {row.chain_position}: transition from {payload['from']},"
                    f" but the run was {run.status}"
                )
            run.status = str(payload["to"])
            run.transitions.append(
                Transition(
                    row.chain_position,
                    row.occurred_at,
                    row.actor,
                    str(payload["from"]),
                    run.status,
                    payload.get("error_code"),
                )
            )
        elif row.event_type == "step.recorded":
            run.steps.append(
                ReplayedStep(
                    chain_position=row.chain_position,
                    occurred_at=row.occurred_at,
                    step_index=int(payload["step_index"]),
                    kind=str(payload["kind"]),
                    tool_name=payload.get("tool_name"),
                    status=str(payload["status"]),
                    refusal_reason=payload.get("refusal_reason"),
                    input_hash=str(payload["input_hash"]),
                    output_hash=payload.get("output_hash"),
                    duration_ms=payload.get("duration_ms"),
                )
            )
        elif row.event_type == "human.decided":
            run.human_decisions.append(
                HumanDecision(
                    row.chain_position,
                    row.occurred_at,
                    str(payload["decision"]),
                    payload.get("decided_by"),
                )
            )
        else:
            run.other_events.append(f"{row.chain_position} {row.event_type}")
    return run


def replay_run(conn: Connection[Any], run_id: str) -> ReplayedRun:
    rows = fetch_rows(conn, run_id=run_id)
    run = rebuild(rows)
    run.integrity_problems = verify_rows(rows) + verify_in_database(conn)
    return run


def render_text(run: ReplayedRun) -> str:
    lines = [
        f"Run {run.run_id}",
        f"  agent {run.agent} (tier {run.tier}), trigger {run.trigger}",
        f"  idempotency key {run.idempotency_key}",
        f"  manifest {run.manifest_hash}",
        f"  connection {run.connection_id or '-'}, user {run.user_id or '-'}",
        f"  status now: {run.status}"
        + (f" (human decision: {run.human_decision})" if run.human_decision else ""),
        "",
        "Timeline (from the audit log):",
    ]
    events: list[tuple[int, str]] = []
    for transition in run.transitions:
        moved = (
            f"{transition.from_status} -> {transition.to_status}"
            if transition.from_status
            else f"enqueued as {transition.to_status}"
        )
        error = f" [{transition.error_code}]" if transition.error_code else ""
        events.append(
            (
                transition.chain_position,
                f"  #{transition.chain_position:<6} {transition.occurred_at}  {moved}{error}"
                f"  by {transition.actor}",
            )
        )
    for step in run.steps:
        tool = f" {step.tool_name}" if step.tool_name else ""
        events.append(
            (
                step.chain_position,
                f"  #{step.chain_position:<6} {step.occurred_at}  step {step.step_index}"
                f" {step.kind}{tool}: {step.status}",
            )
        )
    for decision in run.human_decisions:
        events.append(
            (
                decision.chain_position,
                f"  #{decision.chain_position:<6} {decision.occurred_at}  human {decision.decision}"
                f" (by {decision.decided_by or 'operator'})",
            )
        )
    lines.extend(text for _, text in sorted(events))
    lines.append("")
    if run.integrity_problems:
        lines.append("INTEGRITY PROBLEMS:")
        lines.extend(f"  #{p.chain_position}: {p.problem}" for p in run.integrity_problems)
    else:
        lines.append("Audit chain intact: every row's hash and link checks out.")
    return "\n".join(lines)


def render_json(run: ReplayedRun) -> str:
    return json.dumps(run.to_json(), indent=2, sort_keys=True)
