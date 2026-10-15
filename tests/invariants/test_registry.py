"""Threat-model tests: TM-23. See docs/threat-model.md."""

from typing import Any

import psycopg
import pytest
import yaml
from support import SavepointStore

from postern.agents.security_sentinel import DrillEcho
from postern.config import load_config
from postern.core.registry import RegistryError, parse_registry
from postern.core.runtime import Runtime
from postern.core.worker import drain

VALID_AGENT: dict[str, Any] = {
    "description": "test agent",
    "owner": "tests",
    "tier": 0,
    "status": "active",
    "human_gate": False,
    "tools": ["findings.record"],
    "notification_budget": {"realtime_alerts_per_7_days": 0, "digests_per_7_days": 0},
}


def _registry(**agent: Any) -> str:
    return yaml.safe_dump({"version": 1, "agents": {"probe_agent": agent}})


def test_unregistered_agent_cannot_be_enqueued(db: Any, seed: Any) -> None:
    """An unregistered agent can't be enqueued."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="not registered"):
        db.execute("select postern.enqueue_run('no_such_agent', 'drill', 'k1')")
    db.rollback()

    # Registered but not runnable: planned or retired agents are refused too.
    for status in ("planned", "retired"):
        seed.agent(f"{status}_probe", status=status)
        with pytest.raises(psycopg.errors.CheckViolation, match=f"is {status}, not active"):
            db.execute(f"select postern.enqueue_run('{status}_probe', 'drill', 'k2')")
        db.rollback()

    # Writing the table directly doesn't get around the registry either.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            "insert into postern.runs (agent, tier, manifest_hash, trigger, idempotency_key)"
            " values ('no_such_agent', 0, %s, 'drill', 'k3')",
            ("0" * 64,),
        )


def test_runtime_refuses_unregistered_agent_at_dequeue(db: Any, seed: Any, tier0: str) -> None:
    """The runtime re-checks registration at dequeue, so an agent removed after enqueue doesn't
    run."""
    seed.agent("retiring_probe")
    retired_run = seed.run("retiring_probe")
    db.execute("select postern.retire_agents_not_in(array['security_sentinel'])")
    store = SavepointStore(db)
    assert store.claim_next_run(tier0, 60, 5) is None, "nothing runnable was handed out"
    run = db.execute(
        "select status::text, error_code from postern.runs where id = %s", (retired_run,)
    ).fetchone()
    assert run == ("failed", "agent_not_registered")
    refusal = db.execute(
        "select payload ->> 'status' from postern.audit_log"
        " where run_id = %s and event_type = 'policy.refused'",
        (retired_run,),
    ).fetchone()
    assert refusal == ("retired",)
    queued = db.execute("select count(*) from pgmq.q_tier0").fetchone()
    assert queued == (0,), "the message was archived, not left to retry"

    # A registered agent the worker has no code for is refused by the worker, not run.
    seed.agent("unknown_probe")
    unknown_run = seed.run("unknown_probe")
    runtime = Runtime(store, None, {}, load_config().agent_limits, visibility_seconds=60)
    assert drain(store, runtime, {}, 60, 5, queue=tier0) == 0
    status = db.execute(
        "select status::text, error_code from postern.runs where id = %s", (unknown_run,)
    ).fetchone()
    assert status == ("failed", "agent_not_implemented")

    # An agent whose manifest changed after its run was enqueued doesn't run either.
    seed.agent("changing_probe", tools=["findings.record"])
    changed_run = seed.run("changing_probe")
    db.execute(
        "update postern.agents set manifest_hash = %s where name = 'changing_probe'", ("1" * 64,)
    )
    claimed = store.claim_next_run(tier0, 60, 5)
    assert claimed is not None
    assert claimed.run_id == changed_run
    store.commit()
    outcome = runtime.execute(claimed, DrillEcho())
    assert (outcome.status, outcome.error_code) == ("failed", "manifest_changed")


@pytest.mark.parametrize(
    "missing",
    ["description", "owner", "tier", "status", "human_gate", "tools", "notification_budget"],
)
def test_registry_rejects_agent_missing_required_fields(missing: str) -> None:
    """The registry validator rejects an agent missing owner, tier, tools, human gate or
    notification budget."""
    parse_registry(_registry(**VALID_AGENT))  # the base agent is valid
    agent = {key: value for key, value in VALID_AGENT.items() if key != missing}
    with pytest.raises(RegistryError, match=missing):
        parse_registry(_registry(**agent))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"tools": ["github.push_postern_branch"]}, "forbidden for tier 0"),
        ({"tier": 2, "tools": ["probe.exposure"]}, "forbidden for tier 2"),
        ({"tier": 3, "tools": ["findings.read"]}, "forbidden for tier 3"),
        ({"tools": ["supabase.query.write_sql"]}, "not in the tool catalog"),
        ({"tools": ["github.merge_pull_request"]}, "not in the tool catalog"),
        ({"tier": 1, "tools": ["proposals.record"]}, "tier 1 agents must have human_gate"),
        (
            {"notification_budget": {"realtime_alerts_per_7_days": 1, "digests_per_7_days": 0}},
            "only tier 2 agents may have a non-zero notification budget",
        ),
    ],
)
def test_registry_rejects_tool_forbidden_for_tier(change: dict[str, Any], message: str) -> None:
    """The validator rejects any tool grant the agent's tier forbids."""
    with pytest.raises(RegistryError, match=message):
        parse_registry(_registry(**(VALID_AGENT | change)))
