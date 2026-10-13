"""Threat-model tests: TM-22. See docs/threat-model.md."""

import pytest


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-22 · prevention lands in Phase 2"
)
def test_audit_log_rows_cannot_be_updated_or_deleted() -> None:
    """UPDATE and DELETE are revoked from every Postern role, and a trigger blocks them."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-22 · prevention lands in Phase 2"
)
def test_audit_log_cannot_be_truncated() -> None:
    """TRUNCATE is revoked and blocked by a statement-level trigger. Row triggers don't fire on
    TRUNCATE."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-22 · prevention lands in Phase 2"
)
def test_audit_log_hash_chain_detects_tampering() -> None:
    """Each row stores the hash of the previous row, so any edit breaks the chain."""
    raise NotImplementedError


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-22 · prevention lands in Phase 2"
)
def test_state_change_without_audit_entry_is_rejected() -> None:
    """A state change without its audit row in the same transaction is rejected."""
    raise NotImplementedError
