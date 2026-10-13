"""Threat-model tests: TM-08, TM-10. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-08 · prevention lands in Phase 5"
)
def test_push_gate_rejects_refs_outside_postern_namespace() -> None:
    """Every push goes through one function that only allows refs/heads/postern/*."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-08 · prevention lands in Phase 5"
)
def test_github_client_exposes_no_merge_or_approve_operations() -> None:
    """Postern's GitHub client has no merge, approve or ruleset-edit operation, and no agent grant
    names one."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-10 · prevention lands in Phase 5"
)
def test_push_gate_rejects_changes_outside_schema_paths() -> None:
    """The push gate only allows changes under the repo's Supabase schema and migration paths."""
    raise NotImplementedError
