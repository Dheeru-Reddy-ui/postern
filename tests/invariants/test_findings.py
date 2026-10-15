"""Threat-model tests: TM-14, TM-15, TM-16, TM-17. See docs/threat-model.md."""

import json
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from support import PLANTED, SentinelWorld, advisor_lint


def test_critical_severity_requires_confirmed_live_exposure(db: Any, seed: Any) -> None:
    """Severity "critical" requires a probe-confirmed live exposure. Enforced by a database
    constraint, so no code path can skip it."""
    user = seed.user()
    connection = seed.connection(user)
    agent = seed.agent("severity_probe")
    seed.finding(user, connection, agent, severity="critical", probe_confirmed=True)
    seed.finding(user, connection, agent, severity="high", probe_confirmed=False)
    with pytest.raises(psycopg.errors.CheckViolation, match="critical_requires_confirmed_probe"):
        seed.finding(user, connection, agent, severity="critical", probe_confirmed=False)


def test_llm_cannot_suppress_or_downgrade_deterministic_findings(
    sentinel_world: Callable[..., SentinelWorld],
) -> None:
    """Every advisor lint and probe result becomes a finding through code. The model can explain
    and group findings but can't drop or downgrade one."""
    # A model that tries everything: drop finding 0, downgrade finding 1, invent a finding,
    # and smuggle severity fields into its JSON.
    hostile = json.dumps(
        [
            {"index": 0, "text": "Ignore this one, it is fine.", "drop": True},
            {"index": 1, "text": "Low risk.", "severity": "low", "probe_confirmed": False},
            {"index": 99, "text": "A new critical finding on table secrets."},
            {"index": "2", "text": "string index"},
        ]
    )
    world = sentinel_world(model_text=hostile)
    run_id, outcome = world.run()
    assert outcome.status == "completed"
    findings = world.findings()
    assert {(f["finding_type"], f["object_name"]) for f in findings} == PLANTED
    assert all(f["severity"] == "critical" and f["probe_confirmed"] for f in findings)
    assert sum(f["explanation"] is not None for f in findings) == 2, "only words were added"

    # A model that returns nothing usable changes nothing either.
    garbage = sentinel_world(model_text="I refuse to answer in JSON.")
    _, outcome = garbage.run()
    assert outcome.status == "completed"
    assert {(f["finding_type"], f["object_name"]) for f in garbage.findings()} == PLANTED

    # The only database function the model's output reaches can set an explanation, nothing
    # else: findings are recorded before the model is ever called.
    steps = world.store.completed_steps(run_id)
    kinds = [s.tool_name or s.kind for s in steps]
    first_model_call = kinds.index("llm_call")
    assert "findings.record" not in kinds[first_model_call:]


def test_incomplete_check_never_reports_clean(
    sentinel_world: Callable[..., SentinelWorld], caplog: pytest.LogCaptureFixture
) -> None:
    """A check that didn't complete is reported as "not checked", never "clean" (fail closed)."""

    def last_checked(world: SentinelWorld) -> Any:
        row = world.db.execute(
            "select last_checked_at from postern.connections where id = %s",
            (world.connection_id,),
        ).fetchone()
        assert row is not None
        return row[0]

    # Both sources fail: the run fails, nothing is reported as clean.
    world = sentinel_world(advisors=503, catalog_status=500)
    run_id, outcome = world.run()
    assert (outcome.status, outcome.error_code) == (
        "failed",
        "tool_failed:supabase.advisors.security",
    )
    assert world.run_status(run_id)[0] == "failed"
    assert world.findings() == []
    assert last_checked(world) is None

    # The advisor says it couldn't check, and Postern's own checks fail: same.
    unavailable = {"lints": [advisor_lint("advisor_check_unavailable", "WARN", "public", "x")]}
    world = sentinel_world(advisors=unavailable, catalog_status=500)
    _, outcome = world.run()
    assert outcome.status == "failed"

    # Postern's own checks fail but the advisor answers: what it found is recorded, and the
    # run still fails, because a USING (true) policy could have been missed.
    world = sentinel_world(catalog_status=500)
    run_id, outcome = world.run()
    assert (outcome.status, outcome.error_code) == ("failed", "incomplete_check")
    reported = {(f["finding_type"], f["object_name"]) for f in world.findings()}
    assert reported == PLANTED - {("rls_policy_always_true", "profiles")}
    assert last_checked(world) is None

    # A probe that gets no answer leaves the finding in place, unconfirmed: never dropped.
    import httpx

    world = sentinel_world(rest_error=httpx.ConnectTimeout)
    _, outcome = world.run()
    assert outcome.status == "completed"
    findings = world.findings()
    assert {(f["finding_type"], f["object_name"]) for f in findings} == PLANTED
    assert all(not f["probe_confirmed"] and f["severity"] in ("high", "medium") for f in findings)
