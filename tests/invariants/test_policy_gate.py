"""Threat-model tests: TM-02. See docs/threat-model.md."""

import logging
from collections.abc import Sequence
from typing import Any

import psycopg
import pytest
from support import REPO_ROOT, SavepointStore

from postern.config import load_config
from postern.core.compiler import compile_registry
from postern.core.policy import Grant, PolicyGate, PolicyRefusal
from postern.core.registry import load_registry
from postern.core.runtime import Action, Finish, RunContext, Runtime, ToolCall
from postern.core.store import StepRecord


class AgentThatWants:
    """An agent whose model has been talked into calling a tool it wasn't granted."""

    name = "gate_probe"

    def __init__(self, tool: str) -> None:
        self.tool = tool

    def next_action(self, ctx: RunContext, history: Sequence[StepRecord]) -> Action:
        if not history:
            return ToolCall("findings.record", {"note": "a granted call first"})
        if len(history) == 1:
            return ToolCall(self.tool, {"branch": "main"})
        return Finish("completed")


def _sentinel_grant() -> Grant:
    compiled = compile_registry(load_registry(REPO_ROOT / "agents" / "registry.yaml"))
    sentinel = compiled["security_sentinel"]
    return Grant.from_manifest(sentinel.manifest, sentinel.manifest_hash)


def test_policy_gate_rejects_out_of_grant_tool_call(
    db: psycopg.Connection[Any], seed: Any, tier0: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The policy gate checks every tool call against the agent's compiled manifest, in code,
    regardless of model output."""
    gate, grant = PolicyGate(), _sentinel_grant()
    gate.check(grant, "probe.exposure")  # granted
    for tool, reason in [
        ("github.push_postern_branch", "tool_not_in_grant"),
        ("supabase.query.write_sql", "tool_not_in_grant"),
        ("notify.send", "tool_not_in_grant"),
    ]:
        with pytest.raises(PolicyRefusal) as refused:
            gate.check(grant, tool)
        assert refused.value.reason == reason

    # Even a grant that somehow lists a tool is checked against the catalog and the tier.
    forged = Grant(
        "forged", grant.tier, frozenset({"rm.rf", "github.push_postern_branch"}), False, "0" * 64
    )
    with pytest.raises(PolicyRefusal, match="tool_not_in_catalog"):
        gate.check(forged, "rm.rf")
    with pytest.raises(PolicyRefusal, match="tool_forbidden_for_tier"):
        gate.check(forged, "github.push_postern_branch")

    # In the runtime: the refused call is never executed, and it is recorded, audited and
    # alerted, and ends the run.
    seed.agent("gate_probe", tools=["findings.record"])
    run_id = seed.run("gate_probe")
    store = SavepointStore(db)
    executed: list[str] = []

    def spy(name: str) -> Any:
        def tool(ctx: Any, args: Any, store: Any) -> Any:
            executed.append(name)
            return {"ok": True}

        return tool

    tools = {name: spy(name) for name in ("findings.record", "github.push_postern_branch")}
    runtime = Runtime(store, None, tools, load_config().agent_limits, visibility_seconds=60)
    claimed = store.claim_next_run(tier0, 60, 5)
    store.commit()
    assert claimed is not None
    assert claimed.run_id == run_id
    with caplog.at_level(logging.CRITICAL, logger="postern.runtime"):
        outcome = runtime.execute(claimed, AgentThatWants("github.push_postern_branch"))

    assert executed == ["findings.record"]
    assert (outcome.status, outcome.error_code) == ("failed", "policy_refusal")
    step = db.execute(
        "select tool_name, status, refusal_reason from postern.steps where run_id = %s"
        " and step_index = 1",
        (run_id,),
    ).fetchone()
    assert step == ("github.push_postern_branch", "refused", "tool_not_in_grant")
    audit = db.execute(
        "select entity_id, payload ->> 'reason' from postern.audit_log"
        " where run_id = %s and event_type = 'policy.refused'",
        (run_id,),
    ).fetchall()
    assert audit == [("github.push_postern_branch", "tool_not_in_grant")]
    alerts = [r for r in caplog.records if getattr(r, "postern_alert", None) == "policy_refusal"]
    assert len(alerts) == 1
