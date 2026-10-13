"""Threat-model tests: TM-21. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-21 · prevention lands in Phase 5"
)
def test_pr_not_opened_unless_all_validations_pass() -> None:
    """A PR is opened only if all four validations pass in the throwaway stack: - the anonymous
    probe returns 0 rows - an owner-scoped read still works - the repo's existing tests
    pass - migrations apply from scratch."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-21 · prevention lands in Phase 5"
)
def test_pr_description_states_production_impact() -> None:
    """The PR description says in plain words what merging changes in the live database."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-21 · prevention lands in Phase 5"
)
def test_merged_fix_is_reprobed_and_reopened_if_still_exposed() -> None:
    """Every merged fix is re-probed. If data is still exposed, the finding reopens and the owner
    is alerted."""
    raise NotImplementedError
