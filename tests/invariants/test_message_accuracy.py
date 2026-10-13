"""Threat-model tests: TM-14. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-14 · prevention lands in Phase 6"
)
def test_user_facing_numbers_trace_to_ledger_rows() -> None:
    """Every number in a user-facing message is filled from ledger or findings rows by a template.
    The model writes prose around facts; it doesn't supply them."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-14 · prevention lands in Phase 6"
)
def test_messages_reference_only_existing_tables_and_columns() -> None:
    """Every table or column named in a message must exist in the project's introspected schema."""
    raise NotImplementedError
