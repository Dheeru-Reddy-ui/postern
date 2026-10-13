"""Threat-model tests: TM-02. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-02 · prevention lands in Phase 3"
)
def test_policy_gate_rejects_out_of_grant_tool_call() -> None:
    """The policy gate checks every tool call against the agent's compiled manifest, in code,
    regardless of model output."""
    raise NotImplementedError
