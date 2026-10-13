"""Threat-model tests: TM-04. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-04 · prevention lands in Phase 3"
)
def test_probe_refuses_project_without_verified_connection() -> None:
    """Every project-scoped operation requires a connections row created from a verified OAuth
    grant that includes that project ref."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-04 · prevention lands in Phase 3"
)
def test_pasted_key_must_belong_to_connected_project() -> None:
    """A pasted key is checked against the connected project before it is stored. For a legacy JWT
    that means its ref claim; for a publishable key, a test request to that project."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-04 · prevention lands in Phase 3"
)
def test_agent_cannot_choose_target_project() -> None:
    """The target project and repo come from the run context. No tool takes a project or repo
    argument that the model can set."""
    raise NotImplementedError
