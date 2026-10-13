"""Threat-model tests: TM-23. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-23 · prevention lands in Phase 2"
)
def test_unregistered_agent_cannot_be_enqueued() -> None:
    """An unregistered agent can't be enqueued."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-23 · prevention lands in Phase 3"
)
def test_runtime_refuses_unregistered_agent_at_dequeue() -> None:
    """The runtime re-checks registration at dequeue, so an agent removed after enqueue doesn't
    run."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-23 · prevention lands in Phase 2"
)
def test_registry_rejects_agent_missing_required_fields() -> None:
    """The registry validator rejects an agent missing owner, tier, tools, human gate or
    notification budget."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-23 · prevention lands in Phase 2"
)
def test_registry_rejects_tool_forbidden_for_tier() -> None:
    """The validator rejects any tool grant the agent's tier forbids."""
    raise NotImplementedError
