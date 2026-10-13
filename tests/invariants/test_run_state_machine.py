"""Threat-model tests: TM-24. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-24 · prevention lands in Phase 2"
)
def test_illegal_run_state_transitions_are_rejected() -> None:
    """Run state transitions are enforced in the database. Illegal transitions are rejected."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-24 · prevention lands in Phase 2"
)
def test_human_gated_run_cannot_complete_without_human_decision() -> None:
    """A run for a human-gated agent can't reach completed without a recorded human decision."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-24 · prevention lands in Phase 5"
)
def test_human_decision_must_come_from_project_owner() -> None:
    """A human decision must come from the owner of the connection, whether through the web or
    through postern-mcp."""
    raise NotImplementedError
