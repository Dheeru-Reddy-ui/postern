"""Threat-model tests: TM-01, TM-03. See docs/threat-model.md."""

import json
from collections.abc import Callable

import pytest
from support import ROW_CANARY, SentinelWorld

from postern.core.untrusted import Prompt


def test_row_values_never_reach_llm_prompt(sentinel_world: Callable[..., SentinelWorld]) -> None:
    """Row values never enter an LLM prompt. Tool outputs are typed, and no type carries row
    values (ADR-004).

    Every planted table in the fake project holds a canary row value that the fake REST API
    would return to anyone who asked for rows. After a full run, the canary must appear
    nowhere Postern writes: not in a prompt, a tool output, a finding or the audit log.
    """
    world = sentinel_world()
    run_id, outcome = world.run()
    assert outcome.status == "completed"
    assert world.model.calls, "the explanation prompt was sent"

    prompts = [call["messages"][0]["content"] for call in world.model.calls]
    stored = world.db.execute(
        "select coalesce(json_agg(x), '[]')::text from ("
        " select prompt::text as x from postern.llm_calls where run_id = %(run)s"
        " union all select output::text from postern.steps where run_id = %(run)s"
        " union all select payload::text from postern.audit_log where run_id = %(run)s"
        " union all select row_to_json(f)::text from postern.findings f"
        "  where f.connection_id = %(connection)s"
        " union all select row::text from postern.warehouse_outbox"
        "  where row ->> 'run_id' = %(run)s::text"
        ") written",
        {"run": run_id, "connection": world.connection_id},
    ).fetchone()
    assert stored is not None
    everything = json.dumps(prompts) + stored[0]
    assert "notes" in everything, "sanity check: the search covers what was written"
    assert ROW_CANARY not in everything

    # The types enforce it: a prompt takes trusted instructions, labeled untrusted text, and
    # facts that are numbers or flags. A row value can't arrive as a fact.
    with pytest.raises(TypeError):
        Prompt().fact("first row", ROW_CANARY)  # type: ignore[arg-type]
