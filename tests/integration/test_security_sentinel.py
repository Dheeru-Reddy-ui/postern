"""Security Sentinel end to end on the ADR-008 testbed, offline.

The real runtime, store, gateway, toolkit and database run; only Supabase (over HTTP), the
model and the token broker are faked. The live run on the real testbed project is the Phase 3
"Done when" check; this test pins the same expectations so a regression fails CI first.
"""

from collections.abc import Callable
from typing import Any

import pytest
from support import PLANTED, SentinelWorld, sample_project

pytestmark = pytest.mark.usefixtures("tier0")


def _reported(world: SentinelWorld) -> set[tuple[str, str]]:
    return {(f["finding_type"], f["object_name"]) for f in world.findings()}


def test_sentinel_finds_every_planted_issue_and_nothing_else(
    sentinel_world: Callable[..., SentinelWorld],
) -> None:
    world = sentinel_world()
    run_id, outcome = world.run()

    assert outcome.status == "completed", outcome
    assert world.run_status(run_id) == ("completed", None)
    assert _reported(world) == PLANTED

    # Every planted exposure was confirmed by an anonymous count, so each is critical.
    for finding in world.findings():
        assert finding["severity"] == "critical", finding
        assert finding["probe_confirmed"] is True
        assert finding["source"] == "probe"
        assert finding["explanation"], "the model's plain-language explanation was attached"

    # Only flagged tables were probed, and the clean near-misses never were.
    probed = {path for method, _, path in world.supabase.calls(f"{world.ref}.supabase.co")}
    assert probed == {"/rest/v1/notes", "/rest/v1/profiles", "/rest/v1/order_summary"}

    connection = world.db.execute(
        "select last_checked_at from postern.connections where id = %s", (world.connection_id,)
    ).fetchone()
    assert connection is not None
    assert connection[0] is not None, "a completed check updates last_checked_at"


def test_sentinel_without_the_advisor_still_finds_every_planted_issue(
    sentinel_world: Callable[..., SentinelWorld],
) -> None:
    world = sentinel_world(advisors=503)
    _, outcome = world.run()
    assert outcome.status == "completed", outcome
    assert _reported(world) == PLANTED
    assert all(f["severity"] == "critical" for f in world.findings())


def test_clean_project_reports_nothing(sentinel_world: Callable[..., SentinelWorld]) -> None:
    clean = [r for r in sample_project() if r.visible_rows == 0]
    world = sentinel_world(clean, advisors={"lints": []})
    _, outcome = world.run()
    assert outcome.status == "completed", outcome
    assert world.findings() == []
    assert world.model.calls == [], "no findings means nothing to explain"
    assert world.supabase.calls(f"{world.ref}.supabase.co") == [], "nothing flagged to probe"


def test_flagged_but_unreadable_table_is_reported_without_critical(
    sentinel_world: Callable[..., SentinelWorld],
) -> None:
    """RLS disabled but no anonymous access: a real problem, not a live exposure."""
    relations = sample_project()
    for relation in relations:
        if relation.name == "notes":
            relation.granted_to = ("authenticated",)
    world = sentinel_world(relations)
    world.run()
    notes = next(f for f in world.findings() if f["object_name"] == "notes")
    assert notes["severity"] == "high"
    assert notes["probe_confirmed"] is False
    assert notes["source"] == "advisor"


def test_second_run_updates_findings_instead_of_duplicating(
    sentinel_world: Callable[..., SentinelWorld],
) -> None:
    world = sentinel_world()
    world.run()
    second, outcome = world.run()
    assert outcome.status == "completed"
    assert len(world.findings()) == len(PLANTED)
    events: list[Any] = world.audit_events(second)
    assert events.count("finding.seen_again") == len(PLANTED)
    assert "finding.found" not in events
